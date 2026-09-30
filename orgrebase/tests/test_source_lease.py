from __future__ import annotations

import pytest

from orgrebase.store import StateStore


def _exercise_fenced_renewal(store: StateStore) -> None:
    with store.transaction() as connection:
        claim = store.claim_source(
            connection, connector_id="source:lease", worker_id="worker:old",
            now=0, lease_seconds=10,
        )
    assert claim is not None
    original = {key: claim[key] for key in ("connector_id", "cursor", "revision", "fence", "lease_owner")}

    rejected = [
        {"worker_id": "worker:other"},
        {"fence": claim["fence"] + 1},
        {"expected_revision": claim["revision"] + 1},
        {"expected_cursor": "wrong"},
    ]
    for changes in rejected:
        arguments = {
            "connector_id": "source:lease", "worker_id": "worker:old",
            "fence": claim["fence"], "expected_revision": claim["revision"],
            "expected_cursor": claim["cursor"], "now": 5, "lease_seconds": 20,
            "max_lease_until": 50,
            **changes,
        }
        with store.transaction() as connection:
            assert store.renew_source_claim(connection, **arguments) is None

    with store.transaction() as connection:
        renewed = store.renew_source_claim(
            connection, connector_id="source:lease", worker_id="worker:old",
            fence=claim["fence"], expected_revision=claim["revision"],
            expected_cursor=claim["cursor"], now=5, lease_seconds=20,
            max_lease_until=50,
        )
    assert renewed is not None and renewed["lease_until"] == 25
    assert {key: renewed[key] for key in original} == original

    with store.transaction() as connection:
        shorter = store.renew_source_claim(
            connection, connector_id="source:lease", worker_id="worker:old",
            fence=claim["fence"], expected_revision=claim["revision"],
            expected_cursor=claim["cursor"], now=6, lease_seconds=1,
            max_lease_until=50,
        )
    assert shorter is not None and shorter["lease_until"] == 25

    with store.transaction() as connection:
        capped = store.renew_source_claim(
            connection, connector_id="source:lease", worker_id="worker:old",
            fence=claim["fence"], expected_revision=claim["revision"],
            expected_cursor=claim["cursor"], now=7, lease_seconds=100,
            max_lease_until=50,
        )
    assert capped is not None and capped["lease_until"] == 50
    with store.transaction() as connection:
        assert store.renew_source_claim(
            connection, connector_id="source:lease", worker_id="worker:old",
            fence=claim["fence"], expected_revision=claim["revision"],
            expected_cursor=claim["cursor"], now=8, lease_seconds=1,
            max_lease_until=40,
        ) is None

    with store.transaction() as connection:
        expired = store.claim_source(
            connection, connector_id="source:expired", worker_id="worker:old",
            now=0, lease_seconds=10,
        )
    with store.transaction() as connection:
        assert store.renew_source_claim(
            connection, connector_id="source:expired", worker_id="worker:old",
            fence=expired["fence"], expected_revision=expired["revision"],
            expected_cursor=expired["cursor"], now=10, lease_seconds=10,
            max_lease_until=30,
        ) is None

    with store.transaction() as connection:
        takeover = store.claim_source(
            connection, connector_id="source:expired", worker_id="worker:new",
            now=10, lease_seconds=10,
        )
    assert takeover is not None and takeover["fence"] == expired["fence"] + 1
    with store.transaction() as connection:
        assert store.renew_source_claim(
            connection, connector_id="source:expired", worker_id="worker:old",
            fence=expired["fence"], expected_revision=expired["revision"],
            expected_cursor=expired["cursor"], now=11, lease_seconds=10,
            max_lease_until=30,
        ) is None
        assert not store.release_source_claim(
            connection, connector_id="source:expired", worker_id="worker:old",
            fence=expired["fence"],
        )
    with store.transaction() as connection, pytest.raises(RuntimeError, match="SOURCE_CHECKPOINT_CONFLICT"):
        store.commit_source_page(
            connection, connector_id="source:expired", expected_cursor=expired["cursor"],
            cursor="old-result", worker_id="worker:old", fence=expired["fence"], now=11,
        )
    with store.transaction() as connection:
        committed = store.commit_source_page(
            connection, connector_id="source:expired", expected_cursor=takeover["cursor"],
            cursor="new-result", worker_id="worker:new", fence=takeover["fence"], now=11,
        )
    assert committed["cursor"] == "new-result" and committed["revision"] == 1


def test_sqlite_source_renewal_is_monotonic_capped_and_fenced() -> None:
    with StateStore(tenant_id="tenant:lease") as store:
        _exercise_fenced_renewal(store)


def test_postgres_source_renewal_is_monotonic_capped_and_fenced(postgres_runtime) -> None:
    database = postgres_runtime(tenant_id="tenant:lease")
    with StateStore(database["runtime_dsn"], tenant_id="tenant:lease", migrate=False) as store:
        _exercise_fenced_renewal(store)
