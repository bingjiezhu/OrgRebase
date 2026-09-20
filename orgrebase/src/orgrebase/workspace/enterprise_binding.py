"""One current organization binding, backed by the existing version pointer."""

from __future__ import annotations

from typing import Any

from sqlalchemy import select

from orgrebase.database import in_transaction, workspace_registry
from orgrebase.digest import sha256_digest
from orgrebase.domain import FreshnessError, IntegrityError, ObjectState, VersionedObject
from orgrebase.workspace.models import EnterpriseBinding

SEED_ID = "enterprise-binding:workspace@r1"
MEDIA = "application/vnd.orgrebase.enterprise-binding+json"
POINTER_ID = "organization:enterprise-binding"
_ENABLED = "enterprise-binding:versioning-enabled@r1"
_EVENT_MEDIA = "application/vnd.orgrebase.change-owner-binding+json"


def lock_binding_scope(workspace: Any, connection: Any | None = None) -> None:
    """Serialize authority decisions with migration, without locking read snapshots."""
    store = workspace.store
    selected = connection if connection is not None else store.connection
    if not in_transaction(selected):
        return
    statement = select(workspace_registry.c.workspace_id).where(
        workspace_registry.c.workspace_id == store.workspace_id)
    if (store.backend == "postgresql" and not store._read_only
            and not getattr(store, "_snapshot_transaction_read_only", False)):
        # The workspace identity never changes. Keep foreign-key KEY SHARE reads available.
        statement = statement.with_for_update(key_share=True)
    row = store.execute(selected, statement).fetchone()
    if row is None:
        raise IntegrityError("STATE_STORE_WORKSPACE_NOT_REGISTERED")


def seed_binding(workspace: Any) -> EnterpriseBinding:
    return EnterpriseBinding.model_validate(workspace.store.load_artifact(SEED_ID, MEDIA).payload)


def _version(workspace: Any) -> VersionedObject | None:
    try:
        version = workspace.store.get_object(POINTER_ID)
    except KeyError:
        try:
            workspace.store.load_artifact(_ENABLED, MEDIA)
        except KeyError:
            return None
        raise IntegrityError("OWNER_BINDING_CURRENT_POINTER_MISSING") from None
    payload = version.payload
    seed = seed_binding(workspace)
    try:
        binding = EnterpriseBinding.model_validate(payload["binding"])
        epochs = payload["resource_authorities"]
        valid = (version.kind == "EnterpriseBindingVersion" and version.state == ObjectState.CURRENT
                 and payload["schema_version"] == "orgrebase.enterprise-binding-version.v1"
                 and payload["seed_digest"] == seed.digest
                 and payload["tenant_id"] == workspace.store.tenant_id
                 and payload["workspace_id"] == workspace.store.workspace_id
                 and binding.organization_id == seed.organization_id
                 and binding.quote_object_id == seed.quote_object_id
                 and binding.domain_pack_digest == seed.domain_pack_digest
                 and [(r.slot_id, r.object_id, r.domain_id) for r in binding.resources]
                 == [(r.slot_id, r.object_id, r.domain_id) for r in seed.resources]
                 and set(epochs) == {r.slot_id for r in binding.resources}
                 and all(isinstance(value, str) and value.startswith("sha256:") for value in epochs.values()))
    except (KeyError, TypeError, ValueError) as exc:
        raise IntegrityError("OWNER_BINDING_VERSION_INVALID") from exc
    if not valid:
        raise IntegrityError("OWNER_BINDING_VERSION_INVALID")
    return version


def current_binding(workspace: Any) -> EnterpriseBinding:
    version = _version(workspace)
    return (EnterpriseBinding.model_validate(version.payload["binding"]) if version else seed_binding(workspace))


def binding_revision(workspace: Any) -> str:
    return binding_context(workspace)["revision"]


def binding_context(workspace: Any) -> dict[str, str]:
    version = _version(workspace)
    seed_digest = str(version.payload["seed_digest"]) if version else seed_binding(workspace).digest
    return {"seed_digest": seed_digest,
            "revision": version.ref if version and version.payload.get("migration_digest") else seed_digest}


def has_owner_migrations(workspace: Any) -> bool:
    version = _version(workspace)
    return version is not None and version.payload.get("migration_digest") is not None


def _seed_resource_authority(seed_digest: str, slot_id: str) -> str:
    return sha256_digest({"seed_digest": seed_digest, "slot_id": slot_id})


def has_resource_owner_migrations(workspace: Any, slot_id: str) -> bool:
    version = _version(workspace)
    if version is None:
        return False
    try:
        return version.payload["resource_authorities"][slot_id] != _seed_resource_authority(
            str(version.payload["seed_digest"]), slot_id)
    except KeyError:
        raise IntegrityError("OWNER_CHANGE_RESOURCE_UNKNOWN") from None


def resource_authority(workspace: Any, slot_id: str) -> str:
    version = _version(workspace)
    if version is not None:
        try:
            return str(version.payload["resource_authorities"][slot_id])
        except KeyError:
            raise IntegrityError("OWNER_CHANGE_RESOURCE_UNKNOWN") from None
    seed = seed_binding(workspace)
    if slot_id not in {item.slot_id for item in seed.resources}:
        raise IntegrityError("OWNER_CHANGE_RESOURCE_UNKNOWN")
    return _seed_resource_authority(seed.digest, slot_id)


def initialize_versioning(workspace: Any, connection: Any) -> VersionedObject:
    lock_binding_scope(workspace, connection)
    current = _version(workspace)
    if current is not None:
        return current
    seed = seed_binding(workspace)
    payload = {"schema_version": "orgrebase.enterprise-binding-version.v1",
               "tenant_id": workspace.store.tenant_id, "workspace_id": workspace.store.workspace_id,
               "seed_digest": seed.digest, "binding": seed.model_dump(mode="json"),
               "resource_authorities": {r.slot_id: resource_authority(workspace, r.slot_id) for r in seed.resources},
               "previous_ref": None, "migration_digest": None}
    version = VersionedObject(id=POINTER_ID, version="r1", kind="EnterpriseBindingVersion",
        label="Organization responsibilities", domain="platform", state=ObjectState.CURRENT,
        payload=payload, sensitivity="INTERNAL", allowed_purposes=("organization_owner_change",))
    workspace.store.insert_version(connection, version, make_current=True)
    workspace.store.save_artifact(connection, _ENABLED, MEDIA, {"seed_digest": seed.digest})
    return version


def activate_binding(workspace: Any, connection: Any, *, binding: EnterpriseBinding,
                     expected_revision: str, slot_id: str, migration_digest: str) -> VersionedObject:
    lock_binding_scope(workspace, connection)
    if binding_revision(workspace) != expected_revision:
        raise FreshnessError("OWNER_CHANGE_BINDING_CHANGED")
    previous = initialize_versioning(workspace, connection)
    current = current_binding(workspace)
    changed = [(old, new) for old, new in zip(current.resources, binding.resources, strict=True) if old != new]
    if (len(changed) != 1 or changed[0][0].slot_id != slot_id
            or changed[0][0].model_copy(update={"owner_id": changed[0][1].owner_id}) != changed[0][1]):
        raise IntegrityError("OWNER_CHANGE_SCOPE_INVALID")
    epochs = dict(previous.payload["resource_authorities"])
    epochs[slot_id] = sha256_digest({"previous": epochs[slot_id], "migration_digest": migration_digest})
    version = VersionedObject.model_validate({**previous.model_dump(mode="json", exclude={"digest"}),
        "version": "migration-" + migration_digest.removeprefix("sha256:"),
        "payload": {**previous.payload, "binding": binding.model_dump(mode="json"),
                    "previous_ref": previous.ref, "migration_digest": migration_digest,
                    "resource_authorities": epochs}})
    workspace.store.insert_version(connection, version, make_current=False)
    workspace.store.promote_version(connection, POINTER_ID, previous.version, version.version)
    return version


def bind_change_owner(workspace: Any, connection: Any, event: Any) -> None:
    workspace.store.save_artifact(connection, f"change-owner-binding:{event.event_id}@r1", _EVENT_MEDIA,
        {"event_digest": event.digest, "slot_id": event.slot_id,
         "authority": resource_authority(workspace, event.slot_id)})


def require_change_owner(workspace: Any, event: Any) -> None:
    lock_binding_scope(workspace)
    current = current_binding(workspace)
    resource = next((r for r in current.resources if r.slot_id == event.slot_id), None)
    try:
        saved = workspace.store.load_artifact(f"change-owner-binding:{event.event_id}@r1", _EVENT_MEDIA).payload
    except KeyError:
        seed = seed_binding(workspace)
        authority = _seed_resource_authority(seed.digest, event.slot_id)
    else:
        if saved.get("event_digest") != event.digest or saved.get("slot_id") != event.slot_id:
            raise IntegrityError("CHANGE_OWNER_BINDING_INVALID")
        authority = saved.get("authority")
    if (resource is None or event.owner_id != resource.owner_id
            or authority != resource_authority(workspace, event.slot_id)):
        raise FreshnessError("CHANGE_OWNER_REPLAN_REQUIRED")
