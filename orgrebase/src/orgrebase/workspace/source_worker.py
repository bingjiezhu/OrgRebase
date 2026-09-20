"""Map owner-configured source fields into the existing change admission flow."""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from orgrebase.digest import sha256_digest
from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.workspace.dataverse import DataverseSettings, SourceError
from orgrebase.workspace.enterprise_binding import (
    binding_revision,
    has_resource_owner_migrations,
    lock_binding_scope,
    resource_authority,
)
from orgrebase.workspace.models import ChangeEvent
from orgrebase.workspace.source_bindings import STRUCTURED_SOURCE_SLOTS
from orgrebase.workspace.source_bindings import SourceFieldMapping as SourceFieldMapping

if TYPE_CHECKING:
    from orgrebase.workspace.service import WorkspaceService


class WorkspaceSourceAdmission:
    def __init__(
        self, service: WorkspaceService, settings: DataverseSettings,
        mappings: tuple[SourceFieldMapping, ...],
        *, confirmed_binding_digest: str | None = None,
    ) -> None:
        self.service = service
        self.settings = settings
        self.mappings = mappings
        try:
            service.current_quote()
        except KeyError:
            raise SourceError("SOURCE_BASELINE_FORMATION_REQUIRED") from None
        with service.store.read_snapshot():
            binding = service.enterprise_binding
            self.binding_revision = binding_revision(service)
            self.migrated_slots = frozenset(resource.slot_id for resource in binding.resources
                if has_resource_owner_migrations(service, resource.slot_id))
        resources = {item.slot_id: item for item in binding.resources}
        if not mappings or len({item.slot_id for item in mappings}) != len(mappings):
            raise SourceError("SOURCE_SLOT_MAPPING_AMBIGUOUS")
        if (
            {item.record_id.lower() for item in mappings} != {value.lower() for value in settings.record_ids}
            or {item.field for item in mappings} != set(settings.fields)
            or any(item.slot_id not in resources for item in mappings)
        ):
            raise SourceError("SOURCE_MAPPING_SCOPE_MISMATCH")
        self.resources = resources
        self.admission_digest = sha256_digest({
            "schema_version": "orgrebase.source-admission.v1",
            "enterprise_binding": binding.model_dump(mode="json"),
            "domain_pack": service.domain_pack.model_dump(mode="json"),
            "profile": service.profile.model_dump(mode="json"),
            **({"confirmed_binding_digest": confirmed_binding_digest} if confirmed_binding_digest else {}),
            "mappings": [item.model_dump(mode="json") for item in sorted(
                mappings, key=lambda item: (item.record_id.lower(), item.field, item.slot_id),
            )],
        })

    def source_unavailable(self, error: SourceError) -> None:
        code, _, details = str(error).partition(":")
        if code not in {
            "SOURCE_AUTHENTICATION_FAILED", "SOURCE_PERMISSION_DENIED", "SOURCE_ENDPOINT_UNAVAILABLE",
            "SOURCE_CURSOR_EXPIRED", "SOURCE_REQUEST_FAILED", "SOURCE_UNAVAILABLE", "SOURCE_SCHEMA_DRIFT",
            "SOURCE_RESPONSE_INVALID_JSON", "SOURCE_RESPONSE_UNEXPECTED", "SOURCE_REVISION_MISSING",
            "SOURCE_FIELDS_UNAVAILABLE", "SOURCE_CHANGE_TRACKING_REQUIRED", "SOURCE_PAGE_TOO_LARGE",
            "SOURCE_MAPPING_RECONFIRMATION_REQUIRED", "SOURCE_METADATA_QUALIFICATION_REQUIRED",
            "SOURCE_ORGANIZATION_MISMATCH", "SOURCE_METADATA_INCOMPLETE", "SOURCE_METADATA_AMBIGUOUS",
            "SOURCE_SLOT_TYPE_UNSUPPORTED",
        }:
            return
        affected_fields = set(details.split(",")) if code == "SOURCE_FIELDS_UNAVAILABLE" else set(self.settings.fields)
        if code == "SOURCE_SLOT_TYPE_UNSUPPORTED":
            affected_fields = {item.field for item in self.mappings if item.slot_id in STRUCTURED_SOURCE_SLOTS}
        with self.service.store.transaction() as connection:
            lock_binding_scope(self.service, connection)
            if binding_revision(self.service) != self.binding_revision:
                raise SourceError("SOURCE_MAPPING_RECONFIRMATION_REQUIRED")
            for mapping in self.mappings:
                if mapping.field in affected_fields:
                    self.service.invalidate_source(
                        mapping.slot_id,
                        source_ref=f"{self.settings.endpoint}({mapping.record_id.lower()})#{mapping.field}",
                        reason=code, connection=connection,
                    )

    def __call__(
        self, connection: Any, record: Mapping[str, Any], observed_at: str, page_ref: str,
    ) -> None:
        lock_binding_scope(self.service, connection)
        if binding_revision(self.service) != self.binding_revision:
            raise SourceError("SOURCE_MAPPING_RECONFIRMATION_REQUIRED")
        observed_at = record.get("observed_at", observed_at)
        for mapping in self.mappings:
            if mapping.record_id.lower() != record["record_id"]:
                continue
            source_ref = f"{self.settings.endpoint}({record['record_id']})#{mapping.field}"
            value = record.get("fields", {}).get(mapping.field)
            reason = "SOURCE_RECORD_DELETED" if record["deleted"] else (
                "SOURCE_VALUE_NULL" if value is None else None
            )
            if reason is None and mapping.slot_id in STRUCTURED_SOURCE_SLOTS:
                reason = "SOURCE_SLOT_TYPE_UNSUPPORTED"
            if reason is None and (not isinstance(value, str) or not value.strip()):
                reason = "SOURCE_FIELD_TYPE_UNSUPPORTED"
            if reason is None and mapping.transform == "date_only":
                if not re.fullmatch(r"\d{4}-\d{2}-\d{2}(?:T00:00:00(?:Z|\+00:00))?", value):
                    reason = "SOURCE_DATE_ONLY_VALUE_INVALID"
                else:
                    try:
                        value = date.fromisoformat(value[:10]).isoformat()
                    except ValueError:
                        reason = "SOURCE_DATE_ONLY_VALUE_INVALID"
            if reason is None and mapping.value_map:
                if value not in mapping.value_map:
                    reason = "SOURCE_FIELD_MAPPING_MISSING"
                else:
                    value = mapping.value_map[value]
            if reason is not None:
                self.service.invalidate_source(
                    mapping.slot_id, source_ref=source_ref, reason=reason, connection=connection,
                )
                continue
            resource = self.resources[mapping.slot_id]
            current = self.service.store.get_object(resource.object_id)
            identity = sha256_digest({
                "connector": self.settings.connector_id, "record": record["record_id"],
                "revision": record["revision"], "field": mapping.field,
            }).removeprefix("sha256:")
            observation = {
                "source_ref": source_ref, "revision": record["revision"],
                "value_digest": sha256_digest(value), "page_ref": page_ref,
                "observed_at": observed_at,
            }
            observation_id = f"source-observation:{identity}"
            try:
                previous = self.service.store.load_artifact(observation_id, "application/json").payload
            except KeyError:
                self.service.store.save_artifact(connection, observation_id, "application/json", observation)
            else:
                if previous["value_digest"] != observation["value_digest"]:
                    raise SourceError("SOURCE_REVISION_PAYLOAD_CONFLICT")
                observation = previous
            if current.payload.get("canonical_value") == value and current.state in {
                ObjectState.CURRENT, ObjectState.ACTIVE,
            }:
                continue
            operation = "READMIT" if current.state in {
                ObjectState.STALE, ObjectState.QUARANTINED,
            } else "UPDATE"
            event_identity = sha256_digest({
                "observation": identity, "base": current.ref, "operation": operation,
                **({"resource_authority": resource_authority(self.service, mapping.slot_id)}
                   if mapping.slot_id in self.migrated_slots else {}),
            }).removeprefix("sha256:")
            event_id = f"source:{event_identity}"
            try:
                self.service.changes.get(event_id)
            except KeyError:
                pass
            else:
                continue
            payload = current.model_dump(mode="json")
            payload.pop("digest")
            payload.update({
                "version": f"source-{event_identity[:24]}", "state": ObjectState.PROPOSED,
                "payload": {**current.payload, "canonical_value": value,
                            "source_observation_ref": observation_id},
                "source_refs": (*current.source_refs, source_ref, observation["page_ref"]),
                "valid_from": observation["observed_at"],
            })
            event = ChangeEvent(
                event_id=event_id, organization_id=self.service.profile.organization_id,
                slot_id=mapping.slot_id, owner_id=resource.owner_id,
                base_version=current.version, base_digest=current.digest,
                proposal=VersionedObject.model_validate(payload),
                occurred_at=observation["observed_at"],
                operation=operation,
            )
            try:
                self.service.register_change(event, connection=connection, _source_observation_admission=True)
            except IntegrityError as error:
                # These value checks reject before admission writes. Preserve the
                # observation and mark only this source stale, so a rejected
                # upstream value cannot keep unrelated fields behind the cursor.
                # All other integrity failures still roll back the complete page.
                reason = {
                    "CHANGE_EVENT_DATE_INVALID": "SOURCE_DATE_VALUE_INVALID",
                    "CHANGE_EVENT_CURRENCY_INVALID": "SOURCE_CURRENCY_VALUE_INVALID",
                    "CHANGE_EVENT_PRICING_CURRENCY_MISMATCH": "SOURCE_PRICING_CURRENCY_REQUIRES_ATOMIC_CHANGE",
                }.get(str(error))
                if reason is None:
                    raise
                self.service.invalidate_source(
                    mapping.slot_id, source_ref=source_ref, reason=reason, connection=connection,
                )


def run_source_sync(config_path: Path, *, max_pages: int = 10, discover: bool = False) -> dict[str, Any]:
    from orgrebase.auth import (
        AuthenticationError,
        JWTAuthenticator,
        authorize,
        request_authorization,
        request_principal,
    )
    from orgrebase.runtime_config import DeploymentSettings, open_workspace
    from orgrebase.workspace.dataverse import DataverseReader, SourcePage, SourceSynchronizer
    from orgrebase.workspace.source_bindings import (
        active_binding,
        binding_view,
        current_proposal,
        load_source_config,
        mark_unavailable,
        observe_inventory,
        read_inventory,
        record_coverage,
        source_coverage,
    )

    if not 1 <= max_pages <= 100:
        raise ValueError("SOURCE_PAGE_BUDGET_INVALID")
    config = load_source_config(config_path)
    settings = DeploymentSettings.from_environment()
    if settings.mode != "production":
        raise SourceError("SOURCE_WORKER_REQUIRES_AUTHENTICATED_DEPLOYMENT")
    settings = settings.for_workspace(config.workspace_id)
    if settings.source_config is None or settings.source_config.resolve() != config_path.resolve():
        raise SourceError("SOURCE_CONFIGURATION_PATH_MISMATCH")
    if config.source.tenant_id != settings.identity.tenant_id:
        raise SourceError("SOURCE_TENANT_MISMATCH")
    authenticator = JWTAuthenticator(settings.identity)
    principal = authenticator.authenticate(f"Bearer {os.environ.get(config.access_token_variable, '')}")
    authorize(principal, "propose", settings.identity.tenant_id)
    settings.authorize_workspace(principal.subject)

    def check_authorization() -> None:
        renewed = authenticator.authenticate(f"Bearer {os.environ.get(config.access_token_variable, '')}")
        if (renewed.issuer, renewed.subject, renewed.tenant_id, renewed.actor_id) != (
            principal.issuer, principal.subject, principal.tenant_id, principal.actor_id,
        ):
            raise SourceError("SOURCE_WORKER_IDENTITY_CHANGED")
        authorize(renewed, "propose", settings.identity.tenant_id)
        settings.authorize_workspace(renewed.subject)
        if load_source_config(config_path).digest != config.digest:
            raise SourceError("SOURCE_CONFIGURATION_CHANGED")
        request_principal.set(renewed)

    def metadata_token() -> str:
        check_authorization()
        return os.environ.get(config.source_token_variable, "")

    service = open_workspace(settings)
    authorization_token = request_authorization.set(check_authorization)
    principal_token = request_principal.set(principal)
    receiver = None
    try:
        # Retain only enough of the previous confirmed scope to mark its facts
        # unavailable when the next metadata observation contradicts it.
        try:
            previous = current_proposal(service, config)
            prior_maps = tuple(SourceFieldMapping.model_validate({key: value for key, value in row.items() if key != "owner_id"})
                               for row in previous["mappings"])
            prior_settings = config.source.reader_settings(tuple(sorted({row.field for row in prior_maps})),
                record_ids=tuple(sorted({row.record_id for row in prior_maps})))
            receiver = WorkspaceSourceAdmission(service, prior_settings, prior_maps)
        except SourceError:
            pass
        metadata_reader = DataverseReader(config.source.reader_settings(("quoteid",)), metadata_token)
        observe_inventory(service, config, read_inventory(metadata_reader, config))
        if discover:
            return {**binding_view(service, config), "external_writes": 0}
        binding = active_binding(service, config)
        mappings = tuple(SourceFieldMapping.model_validate({key: value for key, value in row.items() if key != "owner_id"})
                         for row in binding["mappings"])
        source_settings = config.source.reader_settings(tuple(sorted({row.field for row in mappings})),
            connector_id=binding["connector_id"], record_ids=tuple(sorted({row.record_id for row in mappings})))
        receiver = WorkspaceSourceAdmission(service, source_settings, mappings,
                                            confirmed_binding_digest=binding["binding_digest"])

        def data_token() -> str:
            check_authorization()
            if active_binding(service, config)["binding_digest"] != binding["binding_digest"]:
                raise SourceError("SOURCE_MAPPING_CHANGED_DURING_SYNC")
            return os.environ.get(config.source_token_variable, "")

        reader = DataverseReader(source_settings, data_token)

        def verify_page(page: SourcePage) -> SourcePage:
            if page.more:
                return page
            if len(source_settings.record_ids) > 32:
                raise SourceError("SOURCE_READBACK_SCOPE_REQUIRES_PARTITION")
            records = {}
            for identity in sorted(source_settings.record_ids):
                row = reader.read_record(identity)
                records[identity] = {**row, "observed_at": service.clock.now()}
            return SourcePage(tuple(records.values()), page.cursor, False,
                {"complete": True, "record_ids": sorted(records), "method": "CURRENT_POINT_READ",
                 "cross_source_atomic": False})

        synchronizer = SourceSynchronizer(service.store, reader,
            worker_id=f"source-worker:{uuid4()}", admit=receiver, admission_digest=receiver.admission_digest,
            retention_seconds=settings.private_retention_seconds,
            verify_page=verify_page,
            page_committed=lambda connection, inbox, checkpoint, page: record_coverage(
                service, config, binding, connection, inbox, checkpoint, page))
        pages = []
        for _ in range(max_pages):
            check_authorization()
            result = synchronizer.sync_page()
            pages.append(result)
            if not result["more"]:
                break
        return {"status": "SYNCED" if pages and not pages[-1]["more"] else "MORE_PAGES_PENDING",
                "pages": pages, "binding_digest": binding["binding_digest"],
                "coverage": source_coverage(service, config), "external_writes": 0,
                "live_qualification": "NOT_ESTABLISHED_BY_SYNC"}
    except (SourceError, AuthenticationError) as error:
        code = error.code if isinstance(error, AuthenticationError) else str(error).split(":", 1)[0]
        mark_unavailable(service, config, code)
        if receiver is not None and isinstance(error, SourceError):
            receiver.source_unavailable(error)
        raise
    finally:
        request_principal.reset(principal_token)
        request_authorization.reset(authorization_token)
        service.close()
