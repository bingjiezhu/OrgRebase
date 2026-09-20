"""Owner-confirmed source mappings over immutable inventories and the shared ledger."""

from __future__ import annotations

import json
import os
import re
import stat
from datetime import timedelta
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlencode

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from orgrebase.auth import AuthenticationError, request_principal
from orgrebase.clock import timestamp, utc_datetime
from orgrebase.digest import sha256_digest
from orgrebase.domain import AuthorizationError
from orgrebase.workspace.approval_authority import identity_for_action, verify_member
from orgrebase.workspace.change_proposals import require_action
from orgrebase.workspace.dataverse import DataverseReader, DataverseSettings, SourceError
from orgrebase.workspace.enterprise_binding import binding_revision, has_owner_migrations, lock_binding_scope

MEDIA = "application/vnd.orgrebase.source-binding+json"
DIGEST = r"^sha256:[0-9a-f]{64}$"
GUID = r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$"
# This connector maps scalar fields; structured proposals use the workspace
# command until a separately qualified structured source adapter exists.
STRUCTURED_SOURCE_SLOTS = frozenset({"quote_basket", "pricing_policy"})


class SourceScope(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    connector_id: str = Field(pattern=r"^[A-Za-z0-9_.:-]{1,80}$")
    tenant_id: str = Field(min_length=1)
    instance_url: str
    entity_set: Literal["quotes"] = "quotes"
    record_ids: tuple[str, ...] = Field(min_length=1, max_length=1000)
    page_size: int = Field(default=100, ge=1, le=1000)
    ca_bundle: str | None = None

    @model_validator(mode="after")
    def validate_scope(self):
        self.reader_settings(("quoteid",))
        if any(not re.fullmatch(GUID, identity) for identity in self.record_ids):
            raise ValueError("SOURCE_CANONICAL_RECORD_IDS_REQUIRED")
        return self

    def reader_settings(self, fields: tuple[str, ...], *, connector_id: str | None = None,
                        record_ids: tuple[str, ...] | None = None) -> DataverseSettings:
        return DataverseSettings(connector_id=connector_id or self.connector_id, tenant_id=self.tenant_id,
            instance_url=self.instance_url, record_ids=record_ids or self.record_ids, fields=fields,
            page_size=self.page_size, ca_bundle=self.ca_bundle)


class SourceBindingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: SourceScope
    workspace_id: str = Field(default="default", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
    organization_id: str = Field(pattern=GUID)
    source_token_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    access_token_variable: str = Field(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
    freshness_seconds: int = Field(default=300, ge=30, le=900)

    @property
    def digest(self) -> str:
        return sha256_digest(self.model_dump(mode="json"))


class SourceFieldMapping(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    record_id: str = Field(pattern=GUID)
    field: str = Field(pattern=r"^[a-z][a-z0-9_]{0,127}$")
    slot_id: str = Field(min_length=1, max_length=128)
    transform: Literal["identity", "date_only"] = "identity"
    value_map: dict[str, str] = Field(default_factory=dict, max_length=128)

    @model_validator(mode="after")
    def bounded_value_mapping(self):
        if any(not key or not value or len(key) > 256 or len(value) > 2000 for key, value in self.value_map.items()):
            raise ValueError("SOURCE_VALUE_MAPPING_INVALID")
        return self


class SourceBindingProposal(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    inventory_digest: str = Field(pattern=DIGEST)
    generation_digest: str = Field(pattern=DIGEST)
    mappings: list[SourceFieldMapping] = Field(min_length=1, max_length=32)


class SourceBindingConfirmation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    proposal_digest: str = Field(pattern=DIGEST)


def load_source_config(path: Path | None) -> SourceBindingConfig:
    if path is None:
        raise ValueError("SOURCE_CONFIG_ABSENT")
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_mode & 0o022 or info.st_size > 65_536:
                raise ValueError("config file")
            raw = stream.read(65_537)
        def unique(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError("duplicate field")
                result[key] = value
            return result
        return SourceBindingConfig.model_validate(json.loads(raw, object_pairs_hook=unique))
    except (ValueError, TypeError, OSError, ValidationError):
        raise ValueError("SOURCE_CONFIG_INVALID") from None


def _scope(workspace: Any, config: SourceBindingConfig) -> None:
    if (workspace.profile.organization_id != config.source.tenant_id
            or workspace.store.workspace_id != config.workspace_id):
        raise SourceError("SOURCE_WORKSPACE_SCOPE_MISMATCH")


def _latest(workspace: Any, config: SourceBindingConfig, *types: str) -> dict[str, Any] | None:
    page = workspace.store.event_page(subject_key="source:" + config.source.connector_id,
                                     event_types=types, descending=True, limit=1)
    return page["items"][0]["payload"] if page["items"] else None


def _event(workspace: Any, connection: Any, config: SourceBindingConfig, name: str, payload: dict[str, Any]) -> None:
    workspace.store.append_event(connection, name, {"kind": "source:" + config.source.connector_id, **payload})


def _artifact(workspace: Any, kind: str, digest: str) -> dict[str, Any]:
    if not re.fullmatch(DIGEST, digest):
        raise SourceError("SOURCE_BINDING_DIGEST_INVALID")
    return workspace.store.load_artifact(f"source-{kind}:{digest[7:]}", MEDIA).payload


def _save(workspace: Any, connection: Any, kind: str, payload: dict[str, Any]) -> str:
    digest = sha256_digest(payload)
    workspace.store.save_artifact(connection, f"source-{kind}:{digest[7:]}", MEDIA, payload)
    return digest


def read_inventory(reader: DataverseReader, config: SourceBindingConfig) -> dict[str, Any]:
    """Called by the credential-bearing worker; no API accepts this payload."""
    who = reader.metadata("/WhoAmI")
    if who.get("OrganizationId") != config.organization_id:
        raise SourceError("SOURCE_ORGANIZATION_MISMATCH")
    entity_fields = ("LogicalName", "MetadataId", "EntitySetName", "PrimaryIdAttribute", "TableType",
                     "DataProviderId", "DataSourceId", "ChangeTrackingEnabled")
    observed_entity = reader.metadata("/EntityDefinitions(LogicalName='quote')?" + urlencode({
        "$select": ",".join(entity_fields),
    }))
    entity = {key: observed_entity[key] for key in entity_fields if key in observed_entity}
    if (entity.get("LogicalName"), entity.get("EntitySetName"), entity.get("PrimaryIdAttribute")) != ("quote", "quotes", "quoteid"):
        raise SourceError("SOURCE_ENTITY_BINDING_MISMATCH")
    if (entity.get("TableType") != "Standard" or entity.get("DataProviderId", "MISSING") is not None
            or entity.get("DataSourceId", "MISSING") is not None or entity.get("ChangeTrackingEnabled") is not True):
        raise SourceError("SOURCE_METADATA_QUALIFICATION_REQUIRED")
    document = reader.metadata("/EntityDefinitions(LogicalName='quote')/Attributes?" + urlencode({
        "$select": "LogicalName,AttributeType,IsValidForRead,DisplayName",
    }))
    rows = document.get("value")
    if not isinstance(rows, list) or len(rows) > 2000 or "@odata.nextLink" in document:
        raise SourceError("SOURCE_METADATA_INCOMPLETE")
    dates = {}
    if any(isinstance(row, dict) and row.get("AttributeType") == "DateTime" for row in rows):
        date_rows = reader.metadata("/EntityDefinitions(LogicalName='quote')/Attributes/Microsoft.Dynamics.CRM.DateTimeAttributeMetadata?" + urlencode({
            "$select": "LogicalName,DateTimeBehavior",
        }))
        if not isinstance(date_rows.get("value"), list) or "@odata.nextLink" in date_rows:
            raise SourceError("SOURCE_METADATA_INCOMPLETE")
        for row in date_rows["value"]:
            name = row.get("LogicalName") if isinstance(row, dict) else None
            if not isinstance(name, str) or name in dates:
                raise SourceError("SOURCE_METADATA_AMBIGUOUS")
            dates[name] = row.get("DateTimeBehavior")
    fields = []
    names = set()
    for row in rows:
        if not isinstance(row, dict):
            raise SourceError("SOURCE_METADATA_INCOMPLETE")
        name = row.get("LogicalName")
        if not isinstance(name, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,127}", name) or name in names:
            raise SourceError("SOURCE_METADATA_AMBIGUOUS")
        names.add(name)
        label = row.get("DisplayName", {}).get("UserLocalizedLabel") if isinstance(row.get("DisplayName"), dict) else None
        label = label.get("Label") if isinstance(label, dict) else None
        label = label if isinstance(label, str) and 0 < len(label) <= 200 else name
        readable = row.get("IsValidForRead") is True
        kind = row.get("AttributeType")
        if not isinstance(kind, str) or not kind:
            raise SourceError("SOURCE_METADATA_INCOMPLETE")
        behavior = dates.get(name)
        transforms = ["identity"] if readable and kind in {"String", "Memo"} else (
            ["date_only"] if readable and kind == "DateTime" and behavior == {"Value": "DateOnly"} else [])
        fields.append({"field": name, "label": label, "type": kind, "readable": readable,
                       "date_behavior": behavior, "transforms": transforms,
                       "gap": None if transforms else "SOURCE_FIELD_SEMANTICS_UNSUPPORTED" if readable else "SOURCE_FIELD_NOT_READABLE"})
    return {"schema_version": "orgrebase.source-inventory.v1", "config_digest": config.digest,
            "organization_id": config.organization_id, "entity": entity,
            "fields": sorted(fields, key=lambda row: row["field"])}


def _generation_matches_binding(workspace: Any, generation: dict[str, Any]) -> bool:
    return (
        generation["enterprise_binding_digest"] == workspace.enterprise_binding.digest
        and generation.get("binding_revision", generation["enterprise_binding_digest"]) == binding_revision(workspace)
    )


def observe_inventory(workspace: Any, config: SourceBindingConfig, inventory: dict[str, Any]) -> dict[str, Any]:
    _scope(workspace, config)
    if inventory.get("config_digest") != config.digest:
        raise SourceError("SOURCE_INVENTORY_SCOPE_MISMATCH")
    with workspace._command_lock, workspace.store.transaction() as connection:
        lock_binding_scope(workspace, connection)
        identity_for_action(workspace, "propose")
        digest = _save(workspace, connection, "inventory", inventory)
        previous = _latest(workspace, config, "SOURCE_INVENTORY_CHANGED")
        previous_generation = _artifact(workspace, "generation", previous["generation_digest"]) if previous else None
        if (previous is None or previous["inventory_digest"] != digest
                or not _generation_matches_binding(workspace, previous_generation)):
            generation = {"inventory_digest": digest, "config_digest": config.digest,
                          "enterprise_binding_digest": workspace.enterprise_binding.digest,
                          **({"binding_revision": binding_revision(workspace)} if has_owner_migrations(workspace) else {}),
                          "previous_generation": previous["generation_digest"] if previous else None,
                          "observed_at": workspace.clock.now()}
            generation_digest = _save(workspace, connection, "generation", generation)
            _event(workspace, connection, config, "SOURCE_INVENTORY_CHANGED", {
                "inventory_digest": digest, "generation_digest": generation_digest})
        else:
            generation_digest = previous["generation_digest"]
        _event(workspace, connection, config, "SOURCE_INVENTORY_VERIFIED", {
            "inventory_digest": digest, "generation_digest": generation_digest, "observed_at": workspace.clock.now()})
    return {"inventory_digest": digest, "generation_digest": generation_digest}


def current_inventory(workspace: Any, config: SourceBindingConfig) -> dict[str, Any]:
    _scope(workspace, config)
    head = _latest(workspace, config, "SOURCE_INVENTORY_CHANGED")
    if head is None:
        raise SourceError("SOURCE_DISCOVERY_REQUIRED")
    inventory = _artifact(workspace, "inventory", head["inventory_digest"])
    generation = _artifact(workspace, "generation", head["generation_digest"])
    if inventory["config_digest"] != config.digest or not _generation_matches_binding(workspace, generation):
        raise SourceError("SOURCE_DISCOVERY_REQUIRED")
    return {**head, "inventory": inventory}


def _fresh_inventory(workspace: Any, config: SourceBindingConfig, current: dict[str, Any]) -> None:
    head = _latest(workspace, config, "SOURCE_INVENTORY_VERIFIED", "SOURCE_BINDING_UNAVAILABLE")
    now = utc_datetime(workspace.clock.now())
    if (head is None or head.get("generation_digest") != current["generation_digest"]
            or not utc_datetime(head["observed_at"]) <= now < utc_datetime(head["observed_at"]) + timedelta(seconds=config.freshness_seconds)):
        raise SourceError("SOURCE_INVENTORY_FRESHNESS_UNKNOWN")


def _validate_mappings(workspace: Any, config: SourceBindingConfig, current: dict[str, Any], mappings: list[SourceFieldMapping]) -> list[dict[str, Any]]:
    fields = {field["field"]: field for field in current["inventory"]["fields"]}
    resources = {resource.slot_id: resource for resource in workspace.enterprise_binding.resources}
    if len({mapping.slot_id for mapping in mappings}) != len(mappings):
        raise SourceError("SOURCE_SLOT_MAPPING_AMBIGUOUS")
    result = []
    for mapping in mappings:
        field, resource = fields.get(mapping.field), resources.get(mapping.slot_id)
        if (mapping.record_id not in config.source.record_ids or resource is None
                or mapping.slot_id not in workspace.domain_pack.mutable_slots
                or field is None or mapping.transform not in field["transforms"]):
            raise SourceError("SOURCE_MAPPING_SCOPE_OR_SEMANTICS_INVALID")
        if mapping.slot_id in STRUCTURED_SOURCE_SLOTS:
            raise SourceError("SOURCE_SLOT_TYPE_UNSUPPORTED")
        result.append({**mapping.model_dump(), "owner_id": resource.owner_id})
    return sorted(result, key=lambda row: row["slot_id"])


def propose_binding(workspace: Any, config: SourceBindingConfig, request: SourceBindingProposal) -> dict[str, Any]:
    request = SourceBindingProposal.model_validate(request.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        lock_binding_scope(workspace, connection)
        identity = identity_for_action(workspace, "propose")
        current = current_inventory(workspace, config)
        _fresh_inventory(workspace, config, current)
        if (request.inventory_digest, request.generation_digest) != (current["inventory_digest"], current["generation_digest"]):
            raise SourceError("SOURCE_INVENTORY_CHANGED")
        mappings = _validate_mappings(workspace, config, current, request.mappings)
        proposal = {"schema_version": "orgrebase.source-binding-proposal.v1", "config_digest": config.digest,
                    "inventory_digest": request.inventory_digest, "generation_digest": request.generation_digest,
                    "enterprise_binding_digest": workspace.enterprise_binding.digest,
                    "mappings": mappings, "proposer": identity, "proposed_at": workspace.clock.now()}
        digest = _save(workspace, connection, "proposal", proposal)
        _event(workspace, connection, config, "SOURCE_BINDING_PROPOSED", {"proposal_digest": digest})
    return binding_view(workspace, config)


def current_proposal(workspace: Any, config: SourceBindingConfig) -> dict[str, Any]:
    current = current_inventory(workspace, config)
    head = _latest(workspace, config, "SOURCE_BINDING_PROPOSED")
    if head is None:
        raise SourceError("SOURCE_MAPPING_CONFIRMATION_REQUIRED")
    proposal = _artifact(workspace, "proposal", head["proposal_digest"])
    if (proposal["config_digest"] != config.digest or proposal["generation_digest"] != current["generation_digest"]
            or proposal["enterprise_binding_digest"] != workspace.enterprise_binding.digest):
        raise SourceError("SOURCE_MAPPING_RECONFIRMATION_REQUIRED")
    return {**proposal, "proposal_digest": head["proposal_digest"]}


def _decision_key(proposal_digest: str, owner_id: str, *, revoked: bool = False) -> str:
    return f"source-{'revocation' if revoked else 'confirmation'}:" + sha256_digest({"proposal": proposal_digest, "owner": owner_id})[7:]


def _decisions(workspace: Any, proposal: dict[str, Any]) -> list[dict[str, Any]]:
    results = []
    for owner in sorted({mapping["owner_id"] for mapping in proposal["mappings"]}):
        key = _decision_key(proposal["proposal_digest"], owner)
        try:
            confirmation = workspace.store.load_artifact(key, MEDIA).payload
        except KeyError:
            results.append({"owner_id": owner, "status": "PENDING"})
            continue
        if workspace.store.artifact_exists(_decision_key(proposal["proposal_digest"], owner, revoked=True)):
            results.append({"owner_id": owner, "status": "REVOKED"})
            continue
        try:
            verify_member(workspace, confirmation["identity"], "approve")
        except AuthenticationError:
            results.append({"owner_id": owner, "status": "AUTHORITY_REVOKED"})
        else:
            results.append({"owner_id": owner, "status": "CONFIRMED", "confirmation_digest": sha256_digest(confirmation)})
    return results


def confirm_binding(workspace: Any, config: SourceBindingConfig, proposal_id: str,
                    request: SourceBindingConfirmation, *, revoke: bool = False) -> dict[str, Any]:
    request = SourceBindingConfirmation.model_validate(request.model_dump())
    with workspace._command_lock, workspace.store.transaction() as connection:
        lock_binding_scope(workspace, connection)
        identity = identity_for_action(workspace, "approve")
        proposal = current_proposal(workspace, config)
        if not revoke:
            _fresh_inventory(workspace, config, current_inventory(workspace, config))
        if proposal_id != proposal["proposal_digest"][7:] or request.proposal_digest != proposal["proposal_digest"]:
            raise SourceError("SOURCE_PROPOSAL_DIGEST_MISMATCH")
        if identity["actor_id"] not in {mapping["owner_id"] for mapping in proposal["mappings"]}:
            raise AuthorizationError("SOURCE_ORIGINAL_OWNER_REQUIRED")
        verify_member(workspace, identity, "approve")
        key = _decision_key(request.proposal_digest, identity["actor_id"], revoked=revoke)
        if not revoke and workspace.store.artifact_exists(_decision_key(request.proposal_digest, identity["actor_id"], revoked=True)):
            raise SourceError("SOURCE_REVOKED_PROPOSAL_REQUIRES_REVISION")
        if not workspace.store.artifact_exists(key):
            decision = {"proposal_digest": request.proposal_digest, "inventory_digest": proposal["inventory_digest"],
                        "generation_digest": proposal["generation_digest"], "identity": identity,
                        "decided_at": workspace.clock.now(), "decision": "REVOKED" if revoke else "CONFIRMED"}
            workspace.store.save_artifact(connection, key, MEDIA, decision)
            _event(workspace, connection, config, "SOURCE_BINDING_REVOKED" if revoke else "SOURCE_BINDING_CONFIRMED", {
                "proposal_digest": request.proposal_digest, "owner_id": identity["actor_id"], "decision_digest": sha256_digest(decision)})
    return binding_view(workspace, config)


def active_binding(workspace: Any, config: SourceBindingConfig) -> dict[str, Any]:
    require_action(workspace, "read")
    proposal = current_proposal(workspace, config)
    if any(mapping["slot_id"] in STRUCTURED_SOURCE_SLOTS for mapping in proposal["mappings"]):
        raise SourceError("SOURCE_SLOT_TYPE_UNSUPPORTED")
    _fresh_inventory(workspace, config, current_inventory(workspace, config))
    decisions = _decisions(workspace, proposal)
    if any(item["status"] != "CONFIRMED" for item in decisions):
        raise SourceError("SOURCE_MAPPING_CONFIRMATION_REQUIRED")
    result = {**proposal, "confirmations": decisions}
    return {**result, "binding_digest": sha256_digest(result),
            "connector_id": config.source.connector_id + ":" + proposal["proposal_digest"][7:39]}


def mark_unavailable(workspace: Any, config: SourceBindingConfig, reason: str) -> None:
    with workspace.store.transaction() as connection:
        lock_binding_scope(workspace, connection)
        _event(workspace, connection, config, "SOURCE_BINDING_UNAVAILABLE", {"reason": reason, "observed_at": workspace.clock.now()})


def record_coverage(workspace: Any, config: SourceBindingConfig, binding: dict[str, Any], connection: Any,
                    inbox: dict[str, Any], checkpoint: dict[str, Any], page_ref: str) -> None:
    """Called within the page-admission transaction, after its cursor CAS."""
    lock_binding_scope(workspace, connection)
    current = active_binding(workspace, config)
    if current["binding_digest"] != binding["binding_digest"]:
        raise SourceError("SOURCE_MAPPING_CHANGED_DURING_SYNC")
    connector, revision = binding["connector_id"], checkpoint["revision"]
    records = {}
    if revision > 1:
        previous = workspace.store.load_artifact(f"source-coverage:{connector}:{revision - 1}", MEDIA).payload
        records = dict(previous["records"])
    for row in inbox["records"]:
        records[row["record_id"]] = {"revision": row.get("revision"), "deleted": row["deleted"],
            "field_digests": {field: sha256_digest(value) for field, value in row.get("fields", {}).items()},
            "observed_at": row.get("observed_at", inbox["observed_at"])}
    record_ids = sorted({mapping["record_id"] for mapping in binding["mappings"]})
    fields = sorted({mapping["field"] for mapping in binding["mappings"]})
    reasons = []
    if inbox["more"]:
        reasons.append("SOURCE_PAGINATION_INCOMPLETE")
    if set(record_ids) - set(records):
        reasons.append("SOURCE_RECORDS_NOT_OBSERVED")
    readback = inbox.get("readback", {})
    if not inbox["more"] and (readback.get("complete") is not True or readback.get("record_ids") != record_ids):
        reasons.append("SOURCE_CURRENT_READBACK_REQUIRED")
    payload = {"schema_version": "orgrebase.source-coverage.v1", "status": "UNKNOWN" if reasons else "COMPLETE",
        "reasons": reasons, "source_config_digest": config.digest, "source_binding_digest": binding["binding_digest"],
        "inventory_digest": binding["inventory_digest"], "generation_digest": binding["generation_digest"],
        "connector_id": connector, "record_ids": record_ids, "fields": fields,
        "cursor_revision": revision, "cursor_digest": sha256_digest(checkpoint["cursor"]),
        "page_ref": page_ref, "page_digest": sha256_digest(inbox), "records": records,
        "readback": readback,
        "observed_at": inbox["observed_at"],
        "expires_at": timestamp(min([utc_datetime(inbox["observed_at"]),
            *(utc_datetime(row["observed_at"]) for row in records.values())]) + timedelta(seconds=config.freshness_seconds))}
    workspace.store.save_artifact(connection, f"source-coverage:{connector}:{revision}", MEDIA, payload)
    _event(workspace, connection, config, "SOURCE_COVERAGE_COMMITTED", {
        "connector_id": connector, "cursor_revision": revision, "coverage_digest": sha256_digest(payload)})


def source_coverage(workspace: Any, config: SourceBindingConfig, *, now: str | None = None,
                    connection: Any | None = None) -> dict[str, Any]:
    """Read persisted coverage in the caller's existing store/transaction."""
    value = {"schema_version": "orgrebase.source-coverage.v1", "status": "UNKNOWN",
             "source_config_digest": config.digest, "reasons": [], "record_ids": [], "fields": []}
    try:
        if connection is not None and connection is not workspace.store.connection:
            raise SourceError("SOURCE_FOREIGN_CONNECTION")
        binding = active_binding(workspace, config)
        checkpoint = workspace.store.get_source_checkpoint(binding["connector_id"], connection=connection)
        if checkpoint is None or checkpoint["revision"] == 0:
            raise SourceError("SOURCE_BASELINE_NOT_OBSERVED")
        payload = workspace.store.load_artifact(f"source-coverage:{binding['connector_id']}:{checkpoint['revision']}", MEDIA).payload
        value = {**payload, "reasons": list(payload["reasons"])}
        if (payload["source_binding_digest"] != binding["binding_digest"]
                or payload["cursor_digest"] != sha256_digest(checkpoint["cursor"])):
            raise SourceError("SOURCE_COVERAGE_BINDING_MISMATCH")
        if utc_datetime(payload["expires_at"]) <= utc_datetime(now or workspace.clock.now()):
            raise SourceError("SOURCE_COVERAGE_EXPIRED")
        latest = _latest(workspace, config, "SOURCE_BINDING_UNAVAILABLE", "SOURCE_COVERAGE_COMMITTED")
        if latest is None or latest.get("coverage_digest") != sha256_digest(payload):
            raise SourceError("SOURCE_COVERAGE_UNAVAILABLE")
    except (SourceError, AuthenticationError, KeyError) as error:
        value["status"] = "UNKNOWN"
        value["reasons"] = [*value["reasons"], error.code if isinstance(error, AuthenticationError) else str(error).split(":", 1)[0]]
    return {**value, "coverage_digest": sha256_digest(value)}


def binding_view(workspace: Any, config: SourceBindingConfig) -> dict[str, Any]:
    require_action(workspace, "read")
    _scope(workspace, config)
    result = {"schema_version": "orgrebase.source-binding-view.v1", "connector_id": config.source.connector_id,
              "record_ids": list(config.source.record_ids), "inventory": None, "candidates": [], "proposal": None,
              "status": "DISCOVERY_REQUIRED", "allowed_actions": [], "gaps": []}
    try:
        current = current_inventory(workspace, config)
    except SourceError as error:
        result["gaps"] = [{"code": str(error)}]
        return result
    result.update(inventory=current["inventory"], inventory_digest=current["inventory_digest"],
                  generation_digest=current["generation_digest"], status="MAPPING_REQUIRED")
    principal = request_principal.get()
    members = getattr(workspace, "members_for_action", lambda action: ())("approve")
    available_owners = set()
    for member in members:
        try:
            verifier = getattr(workspace, "authorize_workspace_subject", None)
            if verifier:
                verifier(member["subject"])
        except AuthenticationError:
            continue
        available_owners.add(member["actor_id"])
    def normalize(value):
        return re.sub(r"[^a-z0-9]", "", value.lower())
    for resource in workspace.enterprise_binding.resources:
        if resource.slot_id not in workspace.domain_pack.mutable_slots:
            continue
        if resource.slot_id in STRUCTURED_SOURCE_SLOTS:
            result["gaps"].append({"slot_id": resource.slot_id, "code": "SOURCE_SLOT_TYPE_UNSUPPORTED"})
            continue
        matches = [field for field in current["inventory"]["fields"] if field["transforms"] and (
            normalize(field["field"]) == normalize(resource.slot_id)
            or normalize(field["field"].partition("_")[2]) == normalize(resource.slot_id)
            or normalize(field["label"]) == normalize(resource.slot_id))]
        result["candidates"].append({"slot_id": resource.slot_id, "owner_id": resource.owner_id,
            "owner_available": resource.owner_id in available_owners, "fields": matches,
            "candidate_only": True, "reason": "METADATA_NAME_MATCH" if matches else "EXPLICIT_FIELD_SELECTION_REQUIRED"})
        if resource.owner_id not in available_owners:
            result["gaps"].append({"slot_id": resource.slot_id, "owner_id": resource.owner_id, "code": "SOURCE_OWNER_MEMBERSHIP_MISSING"})
    try:
        _fresh_inventory(workspace, config, current)
        require_action(workspace, "propose")
    except (AuthenticationError, SourceError):
        pass
    else:
        result["allowed_actions"].append("PROPOSE")
    try:
        proposal = current_proposal(workspace, config)
        decisions = _decisions(workspace, proposal)
        result["proposal"] = {**proposal, "decisions": decisions}
        result["status"] = "CONFIRMATION_REQUIRED"
        if principal and principal.actor_id in {item["owner_id"] for item in decisions}:
            try:
                require_action(workspace, "approve")
            except AuthenticationError:
                pass
            else:
                own = next(item for item in decisions if item["owner_id"] == principal.actor_id)
                if own["status"] == "PENDING":
                    try:
                        _fresh_inventory(workspace, config, current)
                    except SourceError:
                        pass
                    else:
                        result["allowed_actions"].append("CONFIRM")
                if own["status"] == "CONFIRMED":
                    result["allowed_actions"].append("REVOKE")
        active_binding(workspace, config)
        result["status"] = "CONFIRMED"
    except SourceError as error:
        result["gaps"].append({"code": str(error)})
    result["coverage"] = source_coverage(workspace, config)
    return result
