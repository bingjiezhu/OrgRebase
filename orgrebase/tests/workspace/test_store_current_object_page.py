from __future__ import annotations

import pytest

from orgrebase.digest import sha256_digest
from orgrebase.domain import ObjectState, VersionedObject
from orgrebase.store import StateStore
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime


def _lesson(object_id: str, *, label: str = "lesson") -> VersionedObject:
    return VersionedObject(
        id=object_id, version="v1", kind="Source", label=label,
        domain="experience", state=ObjectState.CURRENT,
        payload={"category": "procedural_advice"},
    )


def _put(store: StateStore, item: VersionedObject) -> None:
    with store.transaction() as connection:
        store.create_current_if_absent(connection, item)
        store.append_event(connection, "LESSON_HEAD_CREATED", {"object_ref": item.ref})


def test_current_object_page_is_bounded_ordered_and_keeps_effective_state() -> None:
    with StateStore(tenant_id="tenant:experience") as store:
        for name in ("lesson:a", "lesson:b", "lesson:c", "other:a"):
            _put(store, _lesson(name))
        first = store.current_object_page(object_id_prefix="lesson:", limit=2)
        assert [item.id for item in first["items"]] == ["lesson:a", "lesson:b"]
        assert first["next_cursor"] == "lesson:b"
        second = store.current_object_page(
            object_id_prefix="lesson:", after=first["next_cursor"], limit=2
        )
        assert [item.id for item in second["items"]] == ["lesson:c"]
        assert second["next_cursor"] is None

        with store.transaction() as connection:
            store.transition_current(connection, "lesson:b", ObjectState.STALE)
            store.append_event(connection, "LESSON_SOURCE_INVALIDATED", {"object_id": "lesson:b"})
        later = store.current_object_page(object_id_prefix="lesson:")
        assert later["items"][1].state is ObjectState.STALE
        with pytest.raises(ValueError, match="STATE_STORE_OBJECT_CURSOR_INVALID"):
            store.current_object_page(object_id_prefix="lesson:", after="other:a")


def test_prefix_wildcard_is_literal_and_does_not_expand_recall_scope() -> None:
    with StateStore(tenant_id="tenant:experience") as store:
        _put(store, _lesson("lesson:%:literal"))
        _put(store, _lesson("lesson:x:ordinary"))
        assert [item.id for item in store.current_object_page(object_id_prefix="lesson:%")["items"]] == [
            "lesson:%:literal"
        ]


def test_postgres_page_preserves_workspace_isolation(postgres_runtime) -> None:
    database = postgres_runtime(tenant_id="tenant:experience")
    with StateStore(database["runtime_dsn"], tenant_id="tenant:experience", migrate=False) as base:
        for workspace_id in ("one", "two"):
            base.register_workspace(
                workspace_id,
                profile_digest=sha256_digest({"profile": workspace_id}),
                pack_digest=None, quote_object_id=f"quote:{workspace_id}",
                created_at="2026-09-28T00:00:00Z",
            )
    for workspace_id in ("one", "two"):
        with StateStore(
            database["runtime_dsn"], tenant_id="tenant:experience",
            workspace_id=workspace_id, migrate=False,
        ) as store:
            _put(store, _lesson("lesson:shared", label=workspace_id))
            page = store.current_object_page(object_id_prefix="lesson:")
            assert [(item.id, item.label) for item in page["items"]] == [
                ("lesson:shared", workspace_id)
            ]
