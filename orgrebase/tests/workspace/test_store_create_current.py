from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest

from orgrebase.domain import IntegrityError, ObjectState, VersionedObject
from orgrebase.store import StateStore
from tests.postgres_support import postgres_cluster as postgres_cluster
from tests.postgres_support import postgres_runtime as postgres_runtime


def head(version: str, *, state: ObjectState = ObjectState.CURRENT) -> VersionedObject:
    return VersionedObject(
        id="skill-head:finance-example",
        version=version,
        kind="Source",
        label="Finance skill head",
        domain="skill-governance",
        state=state,
        payload={"generation": int(version[1:]), "adoption_enabled": False},
        allowed_purposes=("skill_governance",),
    )


def test_first_current_creation_cannot_replace_an_existing_head() -> None:
    with StateStore(":memory:", tenant_id="org:skill-head") as store:
        first = head("g00000000")
        with store.transaction() as connection:
            store.create_current_if_absent(connection, first)
            store.append_event(
                connection, "FINANCE_SKILL_HEAD_BOOTSTRAPPED",
                {"head_ref": first.ref, "head_digest": first.digest},
            )
        assert store.get_pointer(first.id)["version_key"] == first.ref

        with (
            pytest.raises(IntegrityError, match="CURRENT_POINTER_ALREADY_EXISTS"),
            store.transaction() as connection,
        ):
            store.create_current_if_absent(connection, head("g00000001"))
        assert store.get_pointer(first.id)["version_key"] == first.ref
        with pytest.raises(KeyError):
            store.get_object(first.id, "g00000001")
        assert store.verify_event_chain()["status"] == "PASS"


def test_creation_requires_current_state() -> None:
    with StateStore(":memory:", tenant_id="org:skill-head") as store:
        with (
            pytest.raises(IntegrityError, match="CURRENT_POINTER_REQUIRES_CURRENT_VERSION"),
            store.transaction() as connection,
        ):
            store.create_current_if_absent(connection, head("g00000000", state=ObjectState.PROPOSED))
        with pytest.raises(KeyError):
            store.get_pointer("skill-head:finance-example")


def test_two_postgres_creators_cannot_both_create_first_head(postgres_runtime) -> None:
    config = postgres_runtime(tenant_id="org:skill-head")
    with (
        StateStore(config["runtime_dsn"], tenant_id="org:skill-head", migrate=False) as first,
        StateStore(config["runtime_dsn"], tenant_id="org:skill-head", migrate=False) as second,
    ):
        barrier = Barrier(2)

        def create(pair: tuple[StateStore, str]) -> str:
            store, version = pair
            barrier.wait(timeout=10)
            try:
                with store.transaction() as connection:
                    store.create_current_if_absent(connection, head(version))
                return "CREATED"
            except IntegrityError as exc:
                return str(exc)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, ((first, "g00000000"), (second, "g00000001"))))
        assert sorted(results) == ["CREATED", "CURRENT_POINTER_ALREADY_EXISTS"]
        winning = first.get_pointer("skill-head:finance-example")["version"]
        assert winning in {"g00000000", "g00000001"}
        losing = "g00000001" if winning == "g00000000" else "g00000000"
        with pytest.raises(KeyError):
            first.get_object("skill-head:finance-example", losing)
